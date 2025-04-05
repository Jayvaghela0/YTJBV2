from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import threading
import time
import hashlib
import subprocess
from queue import Queue
import logging

app = Flask(__name__)
CORS(app)

# Configuration
DOWNLOAD_FOLDER = "downloads"
COOKIES_FILE = "cookies.txt"
BACKEND_URL = "https://ytjbv2.onrender.com"
TASK_TIMEOUT = 300  # 5 minutes
CLEANUP_INTERVAL = 60  # 1 minute

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Task management
download_tasks = {}
task_queue = Queue()
task_lock = threading.Lock()

# Headers for yt-dlp
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
}

class TaskManager:
    @staticmethod
    def create_task(video_hash):
        with task_lock:
            download_tasks[video_hash] = {
                "status": "queued",
                "created_at": time.time(),
                "last_updated": time.time()
            }
            task_queue.put(video_hash)
            logger.info(f"Created new task: {video_hash}")
        return video_hash

    @staticmethod
    def update_task(video_hash, update_data):
        with task_lock:
            if video_hash in download_tasks:
                download_tasks[video_hash].update(update_data)
                download_tasks[video_hash]["last_updated"] = time.time()
                logger.info(f"Updated task {video_hash}: {update_data}")

    @staticmethod
    def cleanup_old_tasks():
        with task_lock:
            current_time = time.time()
            to_delete = []
            for task_id, task in download_tasks.items():
                if current_time - task["last_updated"] > TASK_TIMEOUT:
                    to_delete.append(task_id)
            
            for task_id in to_delete:
                del download_tasks[task_id]
                logger.info(f"Cleaned up old task: {task_id}")

def background_worker():
    while True:
        video_hash = task_queue.get()
        try:
            task = download_tasks.get(video_hash)
            if not task:
                logger.warning(f"Task not found in worker: {video_hash}")
                continue

            TaskManager.update_task(video_hash, {
                "status": "processing",
                "message": "Starting download..."
            })

            process_task(video_hash)

        except Exception as e:
            logger.error(f"Worker error on task {video_hash}: {str(e)}")
            TaskManager.update_task(video_hash, {
                "status": "failed",
                "error": str(e)
            })
        finally:
            task_queue.task_done()

def process_task(video_hash):
    task = download_tasks.get(video_hash)
    if not task:
        raise ValueError("Task data missing")

    temp_path = os.path.join(DOWNLOAD_FOLDER, f"temp_{video_hash}.mp4")
    final_path = os.path.join(DOWNLOAD_FOLDER, f"{video_hash}.mp4")

    try:
        # Get parameters from task
        params = task.get("params", {})
        url = params.get("url")
        start_time = params.get("start", "0")
        end_time = params.get("end", "")

        def progress_hook(d):
            if d['status'] == 'downloading':
                TaskManager.update_task(video_hash, {
                    "status": "downloading",
                    "progress": d.get('_percent', 0),
                    "message": f"Downloading ({d.get('_percent_str', '0%')})"
                })

        ydl_opts = {
            "format": "bestvideo[ext=mp4]",
            "outtmpl": temp_path,
            "cookiefile": COOKIES_FILE,
            "http_headers": HEADERS,
            "noprogress": True,
            "progress_hooks": [progress_hook]
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        TaskManager.update_task(video_hash, {
            "status": "processing",
            "message": "Processing video..."
        })

        if start_time != "0" or end_time:
            if not end_time:
                end_time = str(info.get("duration", 0))
            
            if clip_video(temp_path, final_path, start_time, end_time):
                os.remove(temp_path)
            else:
                os.rename(temp_path, final_path)
        else:
            os.rename(temp_path, final_path)

        TaskManager.update_task(video_hash, {
            "status": "completed",
            "title": info["title"],
            "download_link": f"{BACKEND_URL}/file/{os.path.basename(final_path)}",
            "message": "Download ready"
        })

    except Exception as e:
        TaskManager.update_task(video_hash, {
            "status": "failed",
            "error": str(e)
        })
        # Cleanup files if they exist
        for path in [temp_path, final_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
        raise

def clip_video(input_path, output_path, start_time, end_time):
    try:
        command = [
            'ffmpeg',
            '-ss', str(start_time),
            '-i', input_path,
            '-to', str(end_time),
            '-c', 'copy',
            '-an',  # Remove audio
            output_path,
            '-y'
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode('utf-8')}")
        return False

def cleanup_old_files():
    while True:
        try:
            now = time.time()
            for filename in os.listdir(DOWNLOAD_FOLDER):
                filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                if os.path.isfile(filepath):
                    file_age = now - os.path.getmtime(filepath)
                    if file_age > TASK_TIMEOUT:
                        try:
                            os.remove(filepath)
                            logger.info(f"Cleaned up old file: {filename}")
                        except Exception as e:
                            logger.error(f"Error cleaning file {filename}: {str(e)}")
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")
        time.sleep(CLEANUP_INTERVAL)

@app.route("/download", methods=["GET"])
def start_download():
    url = request.args.get("url")
    start_time = request.args.get("start", "0")
    end_time = request.args.get("end", "")

    if not url:
        return jsonify({"error": "URL required"}), 400

    video_hash = hashlib.md5((url + str(time.time())).encode()).hexdigest()
    
    # Store all parameters with the task
    TaskManager.create_task(video_hash)
    TaskManager.update_task(video_hash, {
        "params": {
            "url": url,
            "start": start_time,
            "end": end_time
        },
        "status": "queued",
        "message": "Waiting in queue..."
    })

    return jsonify({
        "task_id": video_hash,
        "status": "queued",
        "message": "Download request accepted"
    })

@app.route("/status/<task_id>")
def check_status(task_id):
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({
            "error": "Task not found",
            "code": "not_found"
        }), 404
    
    # Include basic status even if task is not complete
    response = {
        "status": task.get("status", "unknown"),
        "message": task.get("message", ""),
        "progress": task.get("progress", 0)
    }
    
    # Only include these fields if task is completed
    if task["status"] == "completed":
        response.update({
            "title": task.get("title"),
            "download_link": task.get("download_link")
        })
    elif task["status"] == "failed":
        response["error"] = task.get("error", "Unknown error")
    
    return jsonify(response)

@app.route("/file/<filename>")
def serve_file(filename):
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(file_path):
        response = send_file(file_path, as_attachment=True)
        response.headers['Cache-Control'] = 'no-store'
        return response
    return jsonify({"error": "File not found"}), 404

if __name__ == "__main__":
    # Start background worker
    worker_thread = threading.Thread(target=background_worker, daemon=True)
    worker_thread.start()
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    
    # Start task cleanup scheduler
    task_cleanup_thread = threading.Thread(target=TaskManager.cleanup_old_tasks, daemon=True)
    task_cleanup_thread.start()
    
    app.run(debug=True, threaded=True)
