from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import threading
import time
import subprocess

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = "downloads"
COOKIES_FILE = "cookies.txt"
BACKEND_URL = "https://ytjbv.onrender.com"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
}

download_tasks = {}

def delete_after_delay(file_path, delay=300):
    time.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted: {file_path}")
    except Exception as e:
        print(f"Error deleting file: {e}")

def clip_video(input_path, output_path, start, end):
    command = [
        "ffmpeg", "-ss", start, "-i", input_path,
        "-to", end, "-c", "copy", output_path, "-y"
    ]
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def download_video_clip(video_url, video_id, start, end):
    try:
        ydl_opts = {
            "format": "best",
            "outtmpl": f"{DOWNLOAD_FOLDER}/%(title)s.%(ext)s",
            "cookiefile": COOKIES_FILE,
            "http_headers": HEADERS,
            "noprogress": True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            input_path = ydl.prepare_filename(info)
            clip_name = f"{int(time.time())}_clip.mp4"
            output_path = os.path.join(DOWNLOAD_FOLDER, clip_name)

        clip_video(input_path, output_path, start, end)
        threading.Thread(target=delete_after_delay, args=(input_path,)).start()
        threading.Thread(target=delete_after_delay, args=(output_path,)).start()

        download_tasks[video_id] = {
            "status": "completed",
            "title": info["title"],
            "download_link": f"{BACKEND_URL}/file/{clip_name}"
        }

    except Exception as e:
        download_tasks[video_id] = {"status": "failed", "error": str(e)}

@app.route("/")
def home():
    return "YouTube Partial Video Downloader is Running!"

@app.route("/clip", methods=["GET"])
def clip_request():
    url = request.args.get("url")
    start = request.args.get("start")
    end = request.args.get("end")

    if not url or not start or not end:
        return jsonify({"error": "url, start, and end are required"}), 400

    video_id = str(int(time.time()))
    download_tasks[video_id] = {"status": "processing"}

    threading.Thread(target=download_video_clip, args=(url, video_id, start, end)).start()

    return jsonify({"task_id": video_id, "status": "started"})

@app.route("/status/<task_id>")
def check_status(task_id):
    return jsonify(download_tasks.get(task_id, {"error": "Task not found"}))

@app.route("/file/<filename>")
def serve_file(filename):
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": "File not found"}), 404

if __name__ == "__main__":
    app.run(debug=True)
