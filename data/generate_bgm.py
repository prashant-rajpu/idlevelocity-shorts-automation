import math
import struct
import wave
from pathlib import Path
import subprocess

OUT_DIR = Path(__file__).resolve().parent / "bgm"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_RATE = 44100

def generate_ambient_track(filename, chords, bpm=60, total_bars=8):
    """
    Generate a rich ambient pad chord progression with warm harmonics.
    """
    sec_per_beat = 60.0 / bpm
    bar_sec = sec_per_beat * 4
    total_sec = total_bars * bar_sec
    total_samples = int(total_sec * SAMPLE_RATE)
    
    samples = [0.0] * total_samples
    
    # Render each bar chord
    for bar_idx in range(total_bars):
        chord = chords[bar_idx % len(chords)]
        start_sample = int(bar_idx * bar_sec * SAMPLE_RATE)
        chord_samples = int(bar_sec * SAMPLE_RATE)
        
        for freq in chord:
            for s in range(chord_samples):
                idx = start_sample + s
                if idx >= total_samples:
                    break
                t = s / SAMPLE_RATE
                
                # ADSR Envelope for soft pad
                attack = 0.8
                release = 1.0
                if t < attack:
                    env = 0.5 * (1 - math.cos(math.pi * t / attack))
                elif t > (bar_sec - release):
                    rem = bar_sec - t
                    env = 0.5 * (1 - math.cos(math.pi * max(0, rem) / release))
                else:
                    env = 1.0
                
                # Fundamental + soft harmonics
                val = (
                    0.6 * math.sin(2 * math.pi * freq * t)
                    + 0.25 * math.sin(2 * math.pi * (freq * 2) * t)
                    + 0.1 * math.sin(2 * math.pi * (freq * 3) * t)
                    + 0.05 * math.sin(2 * math.pi * (freq * 0.5) * t) # sub octave
                )
                
                # Subtle detuned chorus
                val += 0.3 * math.sin(2 * math.pi * (freq * 1.003) * t)
                val += 0.3 * math.sin(2 * math.pi * (freq * 0.997) * t)
                
                samples[idx] += val * env * 0.15

    # Normalize
    max_val = max(abs(x) for x in samples) or 1.0
    wav_path = OUT_DIR / (filename + ".wav")
    mp3_path = OUT_DIR / (filename + ".mp3")
    
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        int_samples = [int(min(max(s / max_val * 0.85, -1.0), 1.0) * 32767) for s in samples]
        wf.writeframes(struct.pack(f"<{len(int_samples)}h", *int_samples))
    
    # Convert to MP3 with subtle reverb and lowpass warmth
    subprocess.run([
        "ffmpeg", "-y", "-i", str(wav_path),
        "-af", "lowpass=f=2200,aecho=0.8:0.9:500|1000:0.3|0.2",
        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3_path)
    ], check=True)
    wav_path.unlink(missing_ok=True)
    print(f"Generated {mp3_path}")

def main():
    # Am7 -> Fmaj7 -> Cmaj7 -> Gsus4
    chords_focus = [
        [220.00, 261.63, 329.63, 392.00],  # Am7 (A3, C4, E4, G4)
        [174.61, 220.00, 261.63, 329.63],  # Fmaj7 (F3, A3, C4, E4)
        [130.81, 196.00, 261.63, 329.63],  # Cmaj7 (C3, G3, C4, E4)
        [196.00, 261.63, 293.66, 392.00],  # Gsus4 (G3, C4, D4, G4)
    ]
    generate_ambient_track("ambient_focus", chords_focus, bpm=52, total_bars=8)

    # Dm7 -> G7 -> Cmaj7 -> A7
    chords_chill = [
        [146.83, 220.00, 261.63, 349.23],  # Dm7
        [196.00, 246.94, 293.66, 349.23],  # G7
        [130.81, 196.00, 246.94, 329.63],  # Cmaj7
        [220.00, 277.18, 329.63, 392.00],  # A7
    ]
    generate_ambient_track("lofi_chill", chords_chill, bpm=48, total_bars=8)

if __name__ == "__main__":
    main()
