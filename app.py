from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import threading
import time
import hashlib
import subprocess

app = Flask(__name__)
CORS(app)  # Allow all domains

# Configurations
DOWNLOAD_FOLDER = "downloads"
COOKIES_FILE = "cookies.txt"
BACKEND_URL = "https://yt-downloader-3pl3.onrender.com"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Headers for yt-dlp
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
}

download_tasks = {}

def delete_after_delay(file_path, delay=180):
    time.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted: {file_path}")
    except Exception as e:
        print(f"Error deleting file: {e}")

def clip_video(input_path, output_path, start_time, end_time):
    """Clip video using ffmpeg"""
    try:
        command = [
            'ffmpeg',
            '-ss', str(start_time),
            '-i', input_path,
            '-to', str(end_time),
            '-c', 'copy',
            output_path,
            '-y'  # Overwrite without asking
        ]
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e}")
        return False

@app.route("/get_formats", methods=["GET"])
def get_formats():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "URL required"}), 400

    try:
        ydl_opts = {
            "cookiefile": COOKIES_FILE,
            "http_headers": HEADERS,
            "quiet": True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        allowed_resolutions = {144, 240, 360, 480, 720, 1080, 1440}
        allowed_ext = "mp4"
        unique_formats = {}

        for f in info.get("formats", []):
            resolution = f.get("height")
            ext = f.get("ext")
            vcodec = f.get("vcodec")
            acodec = f.get("acodec")

            if resolution in allowed_resolutions and ext == allowed_ext and vcodec != "none":
                if resolution not in unique_formats:
                    unique_formats[resolution] = {
                        "format_id": f.get("format_id"),
                        "resolution": resolution,
                        "ext": ext,
                        "has_audio": acodec != "none"
                    }

        formats = list(unique_formats.values())

        if not formats:
            return jsonify({"error": "No supported formats found"}), 404

        return jsonify({
            "title": info["title"],
            "duration": info.get("duration"),
            "formats": formats
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download", methods=["GET"])
def start_download():
    url = request.args.get("url")
    format_id = request.args.get("format_id")
    start_time = request.args.get("start", "0")
    end_time = request.args.get("end", "")

    if not url or not format_id:
        return jsonify({"error": "URL and Format required"}), 400

    # Generate unique filename
    video_hash = hashlib.md5((url + format_id + str(time.time())).encode()).hexdigest()
    temp_path = os.path.join(DOWNLOAD_FOLDER, f"temp_{video_hash}.mp4")
    final_path = os.path.join(DOWNLOAD_FOLDER, f"{video_hash}.mp4")

    # Start download in new thread
    threading.Thread(
        target=download_and_process_video,
        args=(url, format_id, video_hash, temp_path, final_path, start_time, end_time)
    ).start()

    return jsonify({
        "task_id": video_hash,
        "status": "started",
        "message": "Download and processing started"
    })

def download_and_process_video(video_url, format_id, video_hash, temp_path, final_path, start_time, end_time):
    try:
        # Download full video first
        ydl_opts = {
            "format": format_id,
            "outtmpl": temp_path,
            "cookiefile": COOKIES_FILE,
            "http_headers": HEADERS,
            "noprogress": True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)

        # Process the clip if time parameters are provided
        if start_time != "0" or end_time:
            if not end_time:
                end_time = str(info.get("duration", 0))
            
            if clip_video(temp_path, final_path, start_time, end_time):
                os.remove(temp_path)  # Delete temp file
            else:
                # If clipping failed, use full video
                os.rename(temp_path, final_path)
        else:
            # No clipping needed
            os.rename(temp_path, final_path)

        # Schedule file deletion
        threading.Thread(target=delete_after_delay, args=(final_path, 180)).start()

        download_tasks[video_hash] = {
            "status": "completed",
            "title": info["title"],
            "download_link": f"{BACKEND_URL}/file/{os.path.basename(final_path)}"
        }

    except Exception as e:
        download_tasks[video_hash] = {
            "status": "failed",
            "error": str(e)
        }
        # Clean up temp files if they exist
        for path in [temp_path, final_path]:
            if os.path.exists(path):
                os.remove(path)

@app.route("/status/<task_id>")
def check_status(task_id):
    if task_id in download_tasks:
        return jsonify(download_tasks[task_id])
    return jsonify({"error": "Task not found"}), 404

@app.route("/file/<filename>")
def serve_file(filename):
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(file_path):
        response = send_file(file_path, as_attachment=True)
        response.headers['Cache-Control'] = 'no-store'  # Prevent caching
        return response
    return jsonify({"error": "File not found"}), 404

if __name__ == "__main__":
    app.run(debug=True)
