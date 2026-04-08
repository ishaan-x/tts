#!/usr/bin/env python3
"""
Minds & Machines Podcast Converter
Optimized for Hugging Face Spaces (Docker) + GitLab
Single-narrator mode • en-US-AndrewNeural • -16 LUFS normalized
"""
import os, sys, asyncio, edge_tts, subprocess, re, uuid, time
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template_string
import threading

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB input limit
JOBS = {}
BASE_DIR = Path("/tmp/tts_jobs")
BASE_DIR.mkdir(exist_ok=True)

# 🎨 Mobile-Optimized UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Minds & Machines Podcast Converter</title>
    <style>
        :root { --bg: #0b1120; --card: #111827; --primary: #3b82f6; --text: #f3f4f6; --muted: #9ca3af; }
        body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1rem; display: flex; justify-content: center; min-height: 100vh; }
        .container { max-width: 600px; width: 100%; background: var(--card); padding: 1.25rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
        h1 { font-size: 1.25rem; margin: 0 0 0.5rem; }
        p { color: var(--muted); font-size: 0.875rem; margin: 0 0 1rem; line-height: 1.4; }
        textarea { width: 100%; height: 140px; background: #0f172a; border: 1px solid #1f2937; color: var(--text); padding: 0.75rem; border-radius: 8px; font-size: 0.9rem; resize: vertical; margin-bottom: 0.75rem; }
        .btn { background: var(--primary); color: white; border: none; padding: 0.7rem; border-radius: 8px; font-weight: 500; cursor: pointer; width: 100%; transition: 0.2s; }
        .btn:hover { background: #2563eb; }
        .btn:disabled { background: #374151; cursor: not-allowed; }
        .progress-box { margin-top: 1rem; background: #0f172a; padding: 0.75rem; border-radius: 8px; display: none; }
        .progress-bar { height: 6px; background: #1f2937; border-radius: 3px; overflow: hidden; margin-top: 0.4rem; }
        .progress-fill { height: 100%; background: var(--primary); width: 0%; transition: width 0.3s ease; }
        .status { font-size: 0.8rem; color: var(--muted); margin-top: 0.4rem; }
        .download-btn { display: none; margin-top: 1rem; text-align: center; }
        .download-btn a { background: #10b981; color: white; padding: 0.7rem 1.25rem; text-decoration: none; border-radius: 8px; display: inline-block; font-size: 0.9rem; }
        .error { color: #ef4444; margin-top: 0.75rem; font-size: 0.85rem; min-height: 1.2em; }
        .file-input { margin-bottom: 0.75rem; color: var(--muted); font-size: 0.85rem; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎙️ Podcast Converter</h1>
        <p>Paste your script or upload `.txt`/`.md`. Converts to `-16 LUFS` MP3 ready for Adobe Podcast.</p>
        <textarea id="textInput" placeholder="Paste your podcast script here..."></textarea>        <input type="file" id="fileInput" class="file-input" accept=".txt,.md">
        <button class="btn" id="convertBtn" onclick="startConversion()">Convert to MP3</button>
        
        <div class="progress-box" id="progressBox">
            <div class="status" id="statusText">Initializing...</div>
            <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
        </div>
        
        <div class="download-btn" id="downloadBox">
            <a href="#" id="downloadLink" download="podcast_final.mp3">⬇️ Download MP3</a>
        </div>
        <div class="error" id="errorBox"></div>
    </div>

    <script>
        let jobId = null;
        const btn = document.getElementById('convertBtn');
        const progressBox = document.getElementById('progressBox');
        const progressFill = document.getElementById('progressFill');
        const statusText = document.getElementById('statusText');
        const downloadBox = document.getElementById('downloadBox');
        const downloadLink = document.getElementById('downloadLink');
        const errorBox = document.getElementById('errorBox');

        function startConversion() {
            const text = document.getElementById('textInput').value.trim();
            if (!text) { alert('Please paste text or select a file.'); return; }
            btn.disabled = true; progressBox.style.display = 'block';
            downloadBox.style.display = 'none'; errorBox.textContent = '';

            fetch('/convert', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ text }) })
                .then(res => res.json()).then(data => { jobId = data.job_id; pollProgress(); });
        }

        function pollProgress() {
            if (!jobId) return;
            fetch(`/progress/${jobId}`).then(res => res.json()).then(data => {
                statusText.textContent = data.status;
                progressFill.style.width = `${data.progress}%`;
                if (data.status === 'done') {
                    downloadLink.href = `/download/${jobId}`;
                    downloadBox.style.display = 'block'; btn.disabled = false;
                } else if (data.status.startsWith('error')) {
                    errorBox.textContent = data.status; btn.disabled = false;
                } else { setTimeout(pollProgress, 800); }
            });
        }

        document.getElementById('fileInput').addEventListener('change', function(e) {
            const file = e.target.files[0];            if (file) {
                const reader = new FileReader();
                reader.onload = ev => document.getElementById('textInput').value = ev.target.result;
                reader.readAsText(file);
            }
        });
    </script>
</body>
</html>
"""

# 🧹 Text Cleaning & Chunking
def clean_text(text):
    text = re.sub(r'[*_#~>]', '', text)
    text = text.replace('—', ', ').replace('--', ', ')
    return text

def smart_chunk(text):
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        para_with_pause = para + "\n\n\n"
        if len(current) + len(para_with_pause) > 8000 and current:
            chunks.append(current)
            current = para_with_pause
        else:
            current += para_with_pause
    if current: chunks.append(current)
    return chunks

# 🔊 Conversion Worker
def run_conversion(job_id, text):
    try:
        job_dir = BASE_DIR / job_id
        job_dir.mkdir(exist_ok=True)
        JOBS[job_id] = {"status": "Cleaning text & splitting chunks...", "progress": 5}

        text = clean_text(text)
        chunks = smart_chunk(text)
        total = len(chunks)
        mp3_files = []

        for i, chunk in enumerate(chunks, 1):
            JOBS[job_id] = {"status": f"Converting chunk {i}/{total}...", "progress": int((i/total)*85)}
            out_path = job_dir / f"chunk_{i:03d}.mp3"
            comm = edge_tts.Communicate(chunk, "en-US-AndrewNeural", rate="+5%")
            asyncio.run(comm.save(str(out_path)))
            mp3_files.append(out_path)
            time.sleep(1)  # Rate-limit safe delay
        JOBS[job_id] = {"status": "Merging & normalizing to -16 LUFS...", "progress": 90}
        list_path = job_dir / "filelist.txt"
        with open(list_path, "w") as f:
            for p in mp3_files: f.write(f"file '{p.absolute()}'\n")

        output_file = job_dir / "final_podcast.mp3"
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a", "libmp3lame", "-q:a", "2", str(output_file), "-y"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")

        JOBS[job_id] = {"status": "Cleaning up temp files...", "progress": 95}
        for p in mp3_files + [list_path]:
            if p.exists(): p.unlink()

        JOBS[job_id] = {"status": "done", "progress": 100}
    except Exception as e:
        JOBS[job_id] = {"status": f"error: {str(e)}", "progress": 0}

# 🌐 Routes
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.route('/convert', methods=['POST'])
def convert():
    text = request.json.get('text', '').strip()
    if not text: return jsonify({"error": "No text provided"}), 400
    job_id = str(uuid.uuid4())
    threading.Thread(target=run_conversion, args=(job_id, text), daemon=True).start()
    return jsonify({"job_id": job_id})

@app.route('/progress/<job_id>')
def progress(job_id):
    return jsonify(JOBS.get(job_id, {"status": "not_found", "progress": 0}))

@app.route('/download/<job_id>')
def download(job_id):
    output = BASE_DIR / job_id / "final_podcast.mp3"
    if output.exists(): return send_file(str(output), as_attachment=True, download_name="podcast_final.mp3")
    return "File not found", 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860, debug=False, threaded=True)
