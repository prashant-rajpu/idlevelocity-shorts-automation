import json
import os
import random
import re
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def choose_topic():
    topics = [x.strip() for x in (ROOT / "data/topics.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    history = load_json(ROOT / "data/history.json")
    used = {x.get("topic") for x in history[-60:]}
    available = [x for x in topics if x not in used] or topics
    return random.choice(available), history


def generate_script(topic, cfg):
    prompt = f'''Create one high-retention, viral YouTube Short for {cfg['channel_name']}.
Niche: {cfg['niche']}. Topic: {topic}.
Language: Natural conversational Hinglish written in Devanagari Hindi, mixing everyday English words where Indians naturally use them.
Target Audience: Indian viewers aged 16-30.
Length: 90-115 spoken words (around {cfg['duration_target_seconds']} seconds total).

Structure:
1. First 2 seconds: High-curiosity, psychological pattern-interrupt hook (make them stop scrolling).
2. Friction/Problem: Relatable struggle explained in 1-2 sharp lines.
3. Solution: 2 concrete, actionable principles (simple, tactical).
4. Outro: Memorable 1-line challenge or strong takeaway.

Guidelines:
- No emojis, no sound cues, no scene brackets [like this].
- Natural punctuation (commas and periods) to create clear pauses in narration.
- Return strict JSON only with keys:
  - "title": Catchy title with English keyword and Hindi punch (max 65 chars).
  - "narration": The full spoken script in Devanagari script with natural English words.
  - "description": 2-line SEO-friendly YouTube description in English.
  - "stock_queries": Array of 3 distinct, specific English search queries (2-4 words each) matching each scene: [1. Hook/Problem, 2. Focus/Action, 3. Success/Mindset]. Example: ["tired student scrolling phone", "focused man writing notes", "confident person standing sunrise"]
  - "stock_query": Single fallback query.
'''

    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    models_to_try = [primary_model, "gemini-3.6-flash", "gemini-3-flash", "gemini-2.5-flash", "gemini-2.5-pro"]
    models_to_try = list(dict.fromkeys(models_to_try))

    last_err = None
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.85},
        }
        try:
            res = requests.post(url, json=body, timeout=90)
            if not res.ok:
                print(f"API Error ({model}) [{res.status_code}]: {res.text}")
                res.raise_for_status()
            text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            if not 60 <= len(data["narration"].split()) <= 160:
                raise ValueError("Generated narration failed length check")
            return data
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("Failed to generate script with available Gemini models")


def download_single_video(query, target_path):
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    res = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params={"query": query, "orientation": "portrait", "per_page": 10},
        timeout=60,
    )
    res.raise_for_status()
    videos = res.json().get("videos", [])
    if not videos:
        raise RuntimeError(f"No Pexels footage for: {query}")

    candidates = []
    for video in videos:
        for f in video.get("video_files", []):
            if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0):
                candidates.append((abs(f.get("height", 0) - 1920), f["link"]))

    if not candidates:
        raise RuntimeError(f"No portrait MP4 result for: {query}")

    url = sorted(candidates)[0][1]
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with target_path.open("wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                fh.write(chunk)
    return target_path


def download_multi_stock(queries, cfg):
    """
    Downloads multiple stock video clips and standardizes them to 1080x1920 @ 30fps.
    """
    if not cfg.get("multi_clip", True) or not isinstance(queries, list) or len(queries) < 2:
        query = queries[0] if isinstance(queries, list) and queries else "discipline self improvement focus"
        single_target = OUT / "stock_raw.mp4"
        download_single_video(query, single_target)
        return single_target

    clip_count = min(cfg.get("clip_count", 3), len(queries))
    selected_queries = queries[:clip_count]
    
    standardized_clips = []
    for idx, q in enumerate(selected_queries):
        raw_file = OUT / f"raw_clip_{idx}.mp4"
        std_file = OUT / f"std_clip_{idx}.mp4"
        try:
            print(f"Fetching scene {idx+1}/{len(selected_queries)}: '{q}'...")
            download_single_video(q, raw_file)
            
            # Standardize resolution, aspect ratio, frame rate, remove audio
            subprocess.run([
                "ffmpeg", "-y", "-i", str(raw_file),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                "-r", "30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                str(std_file)
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            standardized_clips.append(std_file)
        except Exception as e:
            print(f"Warning: Failed to process clip for query '{q}': {e}")
            continue

    if not standardized_clips:
        # Fallback to single general query
        fallback_target = OUT / "stock_raw.mp4"
        download_single_video("focus discipline motivation", fallback_target)
        return fallback_target

    if len(standardized_clips) == 1:
        return standardized_clips[0]

    # Concat all standardized clips
    concat_list = OUT / "concat_list.txt"
    concat_content = "\n".join([f"file '{p.resolve()}'" for p in standardized_clips])
    concat_list.write_text(concat_content, encoding="utf-8")

    combined = OUT / "stock_combined.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(combined)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return combined


def make_voice(narration, voice, rate="+12%"):
    audio, subs = OUT / "voice.mp3", OUT / "captions.srt"
    subprocess.run(
        [
            "edge-tts",
            "--voice",
            voice,
            f"--rate={rate}",
            "--text",
            narration,
            "--write-media",
            str(audio),
            "--write-subtitles",
            str(subs),
        ],
        check=True,
    )
    return audio, subs


def choose_bgm():
    bgm_dir = ROOT / "data/bgm"
    if bgm_dir.exists():
        tracks = list(bgm_dir.glob("*.mp3"))
        if tracks:
            return random.choice(tracks)
    return None


def escape_sub_path(path):
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_video(stock, audio, subs, bgm, cfg):
    final = OUT / "short.mp4"
    
    # High-impact, bold, centered subtitle styling with clear margin
    font_size = cfg.get("subtitle_font_size", 28)
    color_map = {
        "yellow": "&H0000FFFF",  # Bright Yellow (BBGGRR)
        "cyan": "&H00FFFF00",
        "white": "&H00FFFFFF",
        "green": "&H0000FF00"
    }
    primary_color = color_map.get(cfg.get("subtitle_color", "yellow"), "&H0000FFFF")
    
    subtitle_filter = (
        f"subtitles='{escape_sub_path(subs)}':force_style="
        f"'FontName=Noto Sans Devanagari,FontSize={font_size},Bold=1,"
        f"PrimaryColour={primary_color},SecondaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,"
        "Outline=4,Shadow=2,Alignment=2,MarginV=320'"
    )
    
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920," + subtitle_filter
    bgm_volume = cfg.get("bgm_volume", 0.12)

    if bgm and Path(bgm).exists() and bgm_volume > 0:
        print(f"Mixing background music: {bgm.name} at volume {bgm_volume}")
        filter_complex = (
            f"[0:v]{vf}[vout]; "
            f"[2:a]volume={bgm_volume}[bgm]; "
            "[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(stock),
            "-i", str(audio),
            "-stream_loop", "-1", "-i", str(bgm),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(final)
        ], check=True)
    else:
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(stock),
            "-i", str(audio),
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(final)
        ], check=True)

    return final


def main():
    OUT.mkdir(exist_ok=True)
    cfg = load_json(ROOT / "config.json")
    topic, history = choose_topic()
    print(f"\n--- Generating Short for topic: '{topic}' ---")
    
    metadata = generate_script(topic, cfg)
    queries = metadata.get("stock_queries") or [metadata.get("stock_query", "focus and discipline")]
    
    stock = download_multi_stock(queries, cfg)
    audio, subs = make_voice(
        metadata["narration"],
        cfg.get("voice", "hi-IN-MadhurNeural"),
        rate=cfg.get("speech_rate", "+12%")
    )
    bgm = choose_bgm()
    video = render_video(stock, audio, subs, bgm, cfg)
    
    metadata.update({
        "topic": topic,
        "video": str(video),
        "hashtags": cfg.get("hashtags", ["#shorts", "#selfimprovement"])
    })
    (OUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✓ Short generation complete!")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
