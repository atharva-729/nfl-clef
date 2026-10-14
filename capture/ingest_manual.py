r"""Fallback for Phases 1-2: ingest screenshots the maintainer took by hand.

The maintainer plays the video fullscreen at 1080p, captions off, cursor hidden, and repeats
  right arrow (jump 5 s) -> wait 2 s -> Win+PrtScn
starting on the first frame of the window.

  python capture/ingest_manual.py                       # everything in Pictures\Screenshots
  python capture/ingest_manual.py --src D:\shots --first "Screenshot (41).png"

Screenshots are taken in numeric order, frame n gets t_target = window_start + interval * n, and
each is copied to data/frames/f_NNNN.png. There is no player to read the real time from, so
t_actual is set to t_target and the row is flagged `manual`.
"""
import argparse
import csv
import re
import shutil
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["frame", "t_target", "t_actual", "flags"]


def number(path):
    """'Screenshot (12).png' -> 12; 'Screenshot.png' or an unnumbered name -> 0."""
    m = re.search(r"\((\d+)\)", path.stem) or re.search(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else 0


def clock(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(Path.home() / "Pictures" / "Screenshots"))
    ap.add_argument("--first", help="file name of the first screenshot to use (earlier ones are ignored)")
    ap.add_argument("--start", type=float, help="video seconds of the first screenshot (default: video.q4_start_video_s)")
    ap.add_argument("--out", default="data/frames")
    ap.add_argument("--index", default="data/frames_index.csv")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    interval = cfg["sample_interval_s"]
    start = args.start if args.start is not None else cfg["video"].get("q4_start_video_s")
    if start is None:
        raise SystemExit("no window start: set video.q4_start_video_s in config.yaml or pass --start")

    shots = sorted(Path(args.src).glob("*.png"), key=lambda p: (number(p), p.name))
    if args.first:
        names = [p.name for p in shots]
        if args.first not in names:
            raise SystemExit(f"{args.first} is not in {args.src}")
        shots = shots[names.index(args.first):]
    if not shots:
        raise SystemExit(f"no .png screenshots in {args.src}")

    print(f"{len(shots)} screenshots, {shots[0].name} .. {shots[-1].name}")
    print(f"first frame -> video time {start:g} s ({clock(start)}), then one every {interval} s, "
          f"last at {clock(start + interval * (len(shots) - 1))}")
    if not args.yes and input("Is the first frame's time right? [y/N] ").strip().lower() != "y":
        raise SystemExit("nothing copied")

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for n, shot in enumerate(shots):
        name = f"f_{n:04d}.png"
        shutil.copyfile(shot, out_dir / name)
        flags = ["manual"]
        with Image.open(shot) as img:
            if img.size != (cfg["capture"]["width"], cfg["capture"]["height"]):
                flags.append(f"size_{img.width}x{img.height}")
        t = start + interval * n
        rows.append({"frame": name, "t_target": f"{t:.1f}", "t_actual": f"{t:.2f}", "flags": " ".join(flags)})
    with (ROOT / args.index).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    odd = sum(1 for r in rows if "size_" in r["flags"])
    print(f"copied {len(rows)} frames to {args.out}, wrote {args.index}" + (f", {odd} not 1920x1080" if odd else ""))


if __name__ == "__main__":
    main()
