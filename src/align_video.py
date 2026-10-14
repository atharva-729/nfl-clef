"""Overlay: find where each captured frame sits in the hosted Q4 video file. No API calls.

  python src/align_video.py

The overlay page plays a hosted WebM of the 4th quarter (config video.file_url). Its clock starts
at 0, 5083.6 s after the YouTube upload's clock that the frames were captured on; the captures' own
timestamps jitter by up to a second, so each is matched. This streams the file once through ffmpeg
at 10 fps and 64x36 grey (nothing is written to disk), then for each capture picks the
best-correlated video frame within
SEARCH_S of the rough offset. Offsets that disagree with their neighbours (static shots match
anywhere) are replaced by the median of the surrounding frames, unless the match itself is strong:
then it is the capture's own timestamp that was off.

Writes data/video_sync.csv: frame, t_youtube, t_file, corr, fixed.
"""
import csv
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from clef_client import load_config

ROOT = Path(__file__).resolve().parent.parent
FPS = 10
W, H = 64, 36
ROUGH_OFFSET = 5083.6      # measured: t_youtube - t_file is 5083.6 s on almost every frame
SEARCH_S = 8
WINDOW = 5                 # neighbours on each side for the median check
MAX_JUMP_S = 1.0
MIN_CORR = 0.8             # a match this good is trusted even when it disagrees with its neighbours


def norm(a):
    a = a.astype(np.float32).ravel()
    return (a - a.mean()) / (a.std() + 1e-6)


def main():
    cfg = load_config()
    url = cfg["video"]["file_url"]
    start = float(json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "json", url],
        capture_output=True, text=True, check=True).stdout)["format"].get("start_time", 0) or 0)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", url, "-an", "-vf", f"fps={FPS},scale={W}:{H}:flags=area,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    video = np.frombuffer(raw, np.uint8).reshape(-1, H * W).astype(np.float32)
    video = (video - video.mean(1, keepdims=True)) / (video.std(1, keepdims=True) + 1e-6)
    print(f"decoded {len(video)} frames ({len(video) / FPS:.0f} s), start_time {start}")

    with (ROOT / "data/frames_index.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    tcol = "t_actual" if "t_actual" in rows[0] else "t_seconds"
    out = []
    for r in rows:
        t = float(r[tcol])
        img = Image.open(ROOT / "data/frames" / r["frame"]).convert("L").resize((W, H), Image.BOX)
        cap = norm(np.asarray(img))
        guess = (t - ROUGH_OFFSET - start) * FPS
        lo, hi = max(0, int(guess - SEARCH_S * FPS)), min(len(video), int(guess + SEARCH_S * FPS) + 1)
        corr = video[lo:hi] @ cap / cap.size
        k = lo + int(corr.argmax())
        out.append({"frame": r["frame"], "t_youtube": t, "t_file": round(start + k / FPS, 2), "corr": round(float(corr.max()), 3), "fixed": 0})

    offs = np.array([o["t_youtube"] - o["t_file"] for o in out])
    for i, o in enumerate(out):
        med = float(np.median(offs[max(0, i - WINDOW):i + WINDOW + 1]))
        if abs(offs[i] - med) > MAX_JUMP_S and o["corr"] < MIN_CORR:
            o["t_file"], o["fixed"] = round(o["t_youtube"] - med, 2), 1
    with (ROOT / "data/video_sync.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    offs = np.array([o["t_youtube"] - o["t_file"] for o in out])
    print(f"{len(out)} frames; offset {offs.min():.1f}..{offs.max():.1f} s; {sum(o['fixed'] for o in out)} fixed; "
          f"corr median {np.median([o['corr'] for o in out]):.2f}, below 0.5: {sum(o['corr'] < 0.5 for o in out)}")


if __name__ == "__main__":
    main()
