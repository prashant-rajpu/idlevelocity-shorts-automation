import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SFX_DIR = ROOT / "data/sfx"


def ensure_sfx():
    """Generates procedural sound effects if they do not exist."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    
    boom = SFX_DIR / "impact_boom.wav"
    if not boom.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=60:duration=1.2,afade=t=out:st=0.1:d=1.1,volume=1.8",
            "-ar", "44100", str(boom)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    whoosh = SFX_DIR / "whoosh_transition.wav"
    if not whoosh.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anoisesrc=d=0.35:c=white:r=44100,bandpass=f=1200:w=800,afade=t=in:st=0:d=0.15,afade=t=out:st=0.15:d=0.2,volume=1.3",
            "-ar", "44100", str(whoosh)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    pop = SFX_DIR / "accent_pop.wav"
    if not pop.exists():
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=1400:duration=0.08,afade=t=out:st=0.01:d=0.07,volume=0.8",
            "-ar", "44100", str(pop)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return {
        "boom": boom,
        "whoosh": whoosh,
        "pop": pop
    }


def build_sfx_track(scene_durations, total_duration, out_path):
    """
    Constructs an aligned SFX track:
    - impact_boom.wav at 0.0s (first scene hook impact)
    - whoosh_transition.wav at every scene cut boundary
    """
    sfx = ensure_sfx()
    boom = sfx["boom"]
    whoosh = sfx["whoosh"]

    cut_points = []
    acc = 0.0
    for d in scene_durations[:-1]:
        acc += d
        cut_points.append(int(acc * 1000))

    inputs = ["-i", str(boom)]
    filter_parts = ["[0:a]volume=1.0[a0]"]
    mix_inputs = ["[a0]"]

    for idx, ms in enumerate(cut_points, start=1):
        inputs.extend(["-i", str(whoosh)])
        filter_parts.append(f"[{idx}:a]adelay={ms}|{ms},volume=0.85[a{idx}]")
        mix_inputs.append(f"[a{idx}]")

    filter_str = "; ".join(filter_parts) + "; " + "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:dropout_transition=0:normalize=0[sfxout]"

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[sfxout]",
        "-t", f"{total_duration:.2f}",
        "-c:a", "pcm_s16le",
        str(out_file)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_file


if __name__ == "__main__":
    res = ensure_sfx()
    for k, v in res.items():
        print(f"SFX ready: {k} -> {v} ({v.stat().st_size} bytes)")
