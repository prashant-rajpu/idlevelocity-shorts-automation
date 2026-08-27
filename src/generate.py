import json
import math
import os
import random
import re
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def choose_topic(cfg):
    topics = [x.strip() for x in (ROOT / "data/topics.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    history = load_json(ROOT / "data/history.json")
    used_topics = {x.get("topic", "").strip().lower() for x in history}
    used_titles = {x.get("title", "").strip().lower() for x in history}
    
    available = [t for t in topics if t.strip().lower() not in used_topics]
    
    if available:
        return random.choice(available), history

    # If all existing topics have been used, dynamically generate a fresh trending topic
    print("All static topics used! Generating a fresh trending viral topic via Gemini...")
    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{primary_model}:generateContent?key={key}"
    
    prompt = f'''Suggest 1 NEW, highly viral, trending YouTube Shorts topic for niche: {cfg['niche']}.
It must NOT be any of these previously used topics:
{json.dumps(list(used_topics)[-25:], ensure_ascii=False)}

Return JSON ONLY: {{"topic": "The single trending topic name in English"}}
'''
    try:
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=30)
        if res.ok:
            new_topic = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            topic_data = json.loads(new_topic)
            topic_name = topic_data.get("topic", "").strip()
            if topic_name and topic_name.lower() not in used_topics:
                # Append to topics.txt for persistence
                with (ROOT / "data/topics.txt").open("a", encoding="utf-8") as f:
                    f.write(f"\n{topic_name}")
                return topic_name, history
    except Exception as e:
        print(f"Notice: Dynamic topic generation fallback to random topic: {e}")
        
    return random.choice(topics), history



def generate_script(topic, cfg):
    prompt = f'''You are a top-tier viral YouTube Shorts creator producing rapid-fire, high-dopamine, high-retention content for channel {cfg['channel_name']}.
Niche: {cfg['niche']}.
Topic: {topic}.
Language: Punchy conversational Hinglish in Devanagari Hindi with natural English words.
Style: Aggressive, energetic, straight-to-the-point, high retention (Alex Hormozi / Ali Abdaal pace).
Target total duration: 30 to 38 seconds (~95-120 spoken words total).

CRITICAL FORMAT REQUIREMENT:
Write 6 to 8 SHORT, FIRING SCENES. Each scene is ONLY 1 short punchy sentence (10-18 words, spoken in 2.5-4 seconds). Visuals change on EVERY scene!

Scene Arc (6-8 beats):
1. Shock Hook (Stop scrolling instantly with high curiosity/pain)
2. The Brutal Truth (Why 99% fail or ruin their morning/focus)
3. The Hidden Friction (What it actually costs you)
4. Rule 1 (Immediate practical action)
5. Rule 2 (Second immediate action)
6. Rule 3 (Third immediate action)
7. Final Call (Aggressive closing challenge to comment/act)

Return STRICT JSON ONLY with structure:
{{
  "title": "High CTR Title with English keyword & emoji (under 60 chars)",
  "description": "2-line engaging YouTube description with relevant hashtags",
  "scenes": [
    {{
      "text": "1 punchy, fast-spoken Hindi/Hinglish line",
      "visual_query": "3-4 precise English search terms for Pexels portrait footage (e.g. 'alarm clock morning wake up', 'glowing phone screen dark bed', 'frustrated person head hands laptop', 'throwing phone away drawer', 'drinking glass cold water', 'writing journal morning desk', 'confident person sunrise walking')",
      "fallback_query": "2 general keywords (e.g. 'phone bed', 'study desk', 'morning walk')"
    }}
  ]
}}
'''

    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    models_to_try = [primary_model, "gemini-3.6-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash"]
    models_to_try = list(dict.fromkeys(models_to_try))

    last_err = None
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.85},
        }
        for attempt in range(3):
            try:
                res = requests.post(url, json=body, timeout=90)
                if res.status_code in (429, 503):
                    wait_time = 2 * (attempt + 1)
                    print(f"Model {model} busy ({res.status_code}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                if not res.ok:
                    print(f"API Error ({model}) [{res.status_code}]: {res.text}")
                    res.raise_for_status()
                text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text)
                
                if "scenes" not in data or not isinstance(data["scenes"], list) or len(data["scenes"]) < 4:
                    raise ValueError("Script must contain at least 4 rapid scenes")
                
                data["narration"] = " ".join([s["text"].strip() for s in data["scenes"] if s.get("text")])
                return data
            except Exception as e:
                last_err = e
                break
    raise last_err or RuntimeError("Failed to generate script with available Gemini models")


def download_single_video(query, target_path, fallback_query=""):
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    
    queries_to_try = [query]
    if fallback_query and fallback_query != query:
        queries_to_try.append(fallback_query)
    words = query.split()
    if len(words) > 2:
        queries_to_try.append(" ".join(words[:2]))
    queries_to_try.extend(["deep focus study", "disciplined routine", "sunrise motivation", "productive workout", "calm thinking"])
    
    for q in queries_to_try:
        try:
            res = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": q, "orientation": "portrait", "per_page": 8},
                timeout=45,
            )
            if not res.ok:
                continue
            videos = res.json().get("videos", [])
            if not videos:
                continue

            candidates = []
            for video in videos:
                for f in video.get("video_files", []):
                    if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0):
                        candidates.append((abs(f.get("height", 0) - 1920), f["link"]))

            if candidates:
                url = sorted(candidates)[0][1]
                with requests.get(url, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with target_path.open("wb") as fh:
                        for chunk in r.iter_content(1024 * 1024):
                            fh.write(chunk)
                return target_path
        except Exception:
            continue

    raise RuntimeError(f"Could not find footage for: {query}")


def get_media_duration(file_path):
    res = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ],
        capture_output=True,
        text=True,
        check=True
    )
    return float(res.stdout.strip())


def sec_to_srt_time(sec):
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = int(sec % 60)
    millis = int(round((sec - int(sec)) * 1000))
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def srt_time_to_sec(ts):
    parts = ts.replace(",", ".").split(":")
    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])


def format_snappy_srt(srt_text, offset_sec, start_idx=1, max_words_per_cue=4):
    """
    Shifts SRT timestamps and splits long sentences into fast-flashing 3-4 word cues.
    """
    lines = srt_text.strip().splitlines()
    shifted_lines = []
    idx = start_idx
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            time_line = lines[i].strip()
            if "-->" in time_line:
                t1, t2 = [x.strip() for x in time_line.split("-->")]
                s1 = srt_time_to_sec(t1) + offset_sec
                s2 = srt_time_to_sec(t2) + offset_sec
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip() and not lines[i].strip().isdigit():
                    text_lines.append(lines[i].strip())
                    i += 1
                
                full_text = " ".join(text_lines)
                words = full_text.split()
                if len(words) > max_words_per_cue:
                    num_chunks = math.ceil(len(words) / max_words_per_cue)
                    chunk_dur = (s2 - s1) / num_chunks
                    for c_idx in range(num_chunks):
                        c_words = words[c_idx * max_words_per_cue : (c_idx + 1) * max_words_per_cue]
                        cs1 = s1 + c_idx * chunk_dur
                        cs2 = min(cs1 + chunk_dur, s2)
                        shifted_lines.append(str(idx))
                        shifted_lines.append(f"{sec_to_srt_time(cs1)} --> {sec_to_srt_time(cs2)}")
                        shifted_lines.append(" ".join(c_words))
                        shifted_lines.append("")
                        idx += 1
                else:
                    shifted_lines.append(str(idx))
                    shifted_lines.append(f"{sec_to_srt_time(s1)} --> {sec_to_srt_time(s2)}")
                    shifted_lines.append(full_text)
                    shifted_lines.append("")
                    idx += 1
        else:
            i += 1
    return "\n".join(shifted_lines), idx


def generate_scene_audio_and_subs(scenes, voice, rate="+28%"):
    audio_files = []
    scene_durations = []
    combined_srt_blocks = []
    current_time_offset = 0.0
    current_sub_idx = 1

    for idx, scene in enumerate(scenes):
        scene_audio = OUT / f"scene_{idx}_audio.mp3"
        scene_subs = OUT / f"scene_{idx}_subs.srt"
        
        subprocess.run(
            [
                "edge-tts",
                "--voice", voice,
                f"--rate={rate}",
                "--text", scene["text"],
                "--write-media", str(scene_audio),
                "--write-subtitles", str(scene_subs),
            ],
            check=True,
        )
        
        dur = get_media_duration(scene_audio)
        scene_durations.append(dur)
        audio_files.append(scene_audio)
        
        if scene_subs.exists():
            srt_content = scene_subs.read_text(encoding="utf-8")
            shifted_block, next_idx = format_snappy_srt(srt_content, current_time_offset, current_sub_idx, max_words_per_cue=4)
            combined_srt_blocks.append(shifted_block)
            current_sub_idx = next_idx

        current_time_offset += dur

    concat_audio_txt = OUT / "concat_audio.txt"
    concat_audio_txt.write_text("\n".join([f"file '{p.resolve()}'" for p in audio_files]), encoding="utf-8")
    
    master_audio = OUT / "voice.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_audio_txt),
        "-c", "copy", str(master_audio)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    master_subs = OUT / "captions.srt"
    master_subs.write_text("\n".join(combined_srt_blocks), encoding="utf-8")

    return master_audio, master_subs, scene_durations


def build_script_matching_video(scenes, scene_durations):
    scene_video_clips = []
    
    for idx, (scene, duration) in enumerate(zip(scenes, scene_durations)):
        raw_clip = OUT / f"raw_scene_{idx}.mp4"
        synced_clip = OUT / f"synced_scene_{idx}.mp4"
        
        query = scene.get("visual_query", "discipline focus motivation")
        fallback = scene.get("fallback_query", "")
        print(f"🎬 Scene {idx+1}/{len(scenes)} [{duration:.2f}s]: Searching '{query}'...")
        
        download_single_video(query, raw_clip, fallback)
        
        # Scale, crop to 1080x1920, and trim to EXACT scene duration
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(raw_clip),
            "-t", f"{duration:.2f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-r", "30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            str(synced_clip)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        scene_video_clips.append(synced_clip)

    concat_video_txt = OUT / "concat_video.txt"
    concat_video_txt.write_text("\n".join([f"file '{p.resolve()}'" for p in scene_video_clips]), encoding="utf-8")
    
    master_video = OUT / "stock_synced.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_video_txt),
        "-c", "copy", str(master_video)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return master_video


def choose_bgm():
    bgm_dir = ROOT / "data/bgm"
    if bgm_dir.exists():
        # Prioritize driving energy tracks
        energy_tracks = list(bgm_dir.glob("*energy*.mp3")) + list(bgm_dir.glob("*pulse*.mp3"))
        if energy_tracks:
            return random.choice(energy_tracks)
        tracks = list(bgm_dir.glob("*.mp3"))
        if tracks:
            return random.choice(tracks)
    return None


def escape_sub_path(path):
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_final_video(stock_video, voice_audio, subs, bgm, cfg):
    final = OUT / "short.mp4"
    duration = get_media_duration(voice_audio)
    print(f"\nFinal Short Duration: {duration:.2f}s (Fast Paced)")
    
    font_size = cfg.get("subtitle_font_size", 28)
    color_map = {
        "yellow": "&H0000FFFF",
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
    
    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,{subtitle_filter}"
    bgm_volume = cfg.get("bgm_volume", 0.15)

    if bgm and Path(bgm).exists() and bgm_volume > 0:
        print(f"Mixing background music: {bgm.name} at volume {bgm_volume}")
        filter_complex = (
            f"[0:v]{vf}[vout]; "
            f"[2:a]volume={bgm_volume}[bgm]; "
            "[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(stock_video),
            "-i", str(voice_audio),
            "-stream_loop", "-1", "-i", str(bgm),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.2f}",
            "-movflags", "+faststart",
            str(final)
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(stock_video),
            "-i", str(voice_audio),
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.2f}",
            "-movflags", "+faststart",
            str(final)
        ]

    subprocess.run(cmd, check=True)
    return final


def main():
    OUT.mkdir(exist_ok=True)
    cfg = load_json(ROOT / "config.json")
    topic, history = choose_topic(cfg)
    print(f"\n==========================================")
    print(f"Generating High-Retention Firing Short: '{topic}'")
    print(f"==========================================")
    
    script_data = generate_script(topic, cfg)
    scenes = script_data["scenes"]
    print(f"Generated {len(scenes)} fast-firing scenes.")
    
    # 1. Synthesize fast audio per scene to get exact sentence timings & merged rapid captions
    voice_audio, master_subs, scene_durations = generate_scene_audio_and_subs(
        scenes,
        cfg.get("voice", "hi-IN-MadhurNeural"),
        rate=cfg.get("speech_rate", "+28%")
    )
    
    # 2. Build precision visual track matching each scene sentence
    synced_video = build_script_matching_video(scenes, scene_durations)
    
    # 3. Choose driving background beat & render final high-retention Short
    bgm = choose_bgm()
    final_short = render_final_video(synced_video, voice_audio, master_subs, bgm, cfg)
    
    metadata = {
        "title": script_data["title"],
        "description": script_data["description"],
        "narration": script_data["narration"],
        "scenes": scenes,
        "topic": topic,
        "video": str(final_short),
        "hashtags": cfg.get("hashtags", ["#shorts", "#selfimprovement"])
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✓ High-retention fast-firing Short ready!")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
