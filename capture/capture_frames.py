"""Phases 0-2: capture still frames from the embedded YouTube player. Nothing is downloaded.

  python capture/capture_frames.py --check                       # Phase 0: is the video embeddable?
  python capture/capture_frames.py --start 0 --end 12000 --interval 60 \
      --out data/frames_coarse --index data/frames_coarse/index.csv   # Phase 1: coarse pass to find Q4
  python capture/capture_frames.py --count 10                    # Phase 1: pilot, 10 frames from the window start
  python capture/capture_frames.py                               # Phase 2: the whole window (resume-safe)

Uses the locally installed Chrome (headed), one tab, one seek every `capture.seek_gap_s`. Frame n is
taken at t_target = start + interval * n and saved as f_NNNN.png; the index logs the player's real
time as t_actual. If an advert, a login or a bot check appears, the script stops: do not work
around it, switch to capture/ingest_manual.py.
"""
import argparse
import csv
import functools
import http.server
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["frame", "t_target", "t_actual", "flags"]
EMBED_DISABLED = (101, 150)


def serve_repo(port):
    """Serve the repo root on localhost unless something (python -m http.server) already does."""
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def read_index(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["frame"]: r for r in csv.DictReader(fh)}


def write_index(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="load the player, report embeddability, capture nothing")
    ap.add_argument("--start", type=float, help="video seconds (default: video.q4_start_video_s)")
    ap.add_argument("--end", type=float, help="video seconds (default: video.q4_end_video_s)")
    ap.add_argument("--count", type=int, help="capture this many frames from --start instead of up to --end")
    ap.add_argument("--interval", type=float)
    ap.add_argument("--out", default="data/frames")
    ap.add_argument("--index", default="data/frames_index.csv")
    ap.add_argument("--redo-flagged", action="store_true", help="re-capture frames whose index row has a flag")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cap, video = cfg["capture"], cfg["video"]
    interval = args.interval or cfg["sample_interval_s"]
    start = args.start if args.start is not None else video.get("q4_start_video_s")
    end = args.end if args.end is not None else video.get("q4_end_video_s")
    if not args.check:
        if start is None or (end is None and args.count is None):
            raise SystemExit("no window: set video.q4_start_video_s / q4_end_video_s in config.yaml or pass --start/--end")
        n_frames = args.count if args.count is not None else int((end - start) // interval) + 1
    out_dir, index_path = ROOT / args.out, ROOT / args.index
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_index(index_path)

    serve_repo(urlparse(cap["page_url"]).port or 80)
    url = f"{cap['page_url']}?v={video['id']}&settle={cap['settle_s']}"
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False,
                                    args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": cap["width"], "height": cap["height"]}, device_scale_factor=1)
        page.goto(url)
        if not page.evaluate("typeof seekTo === 'function'"):
            raise SystemExit(f"{cap['page_url']} is not capture/embed.html (is another server on that port?)")
        try:
            page.wait_for_function("window.ytReady || window.ytError !== null", timeout=30000)
        except Exception:
            raise SystemExit("the player never became ready (blocked, login or bot check?). Stop and report.")
        err = page.evaluate("window.ytError")
        if err is not None:
            verdict = "embedding is DISABLED for this video" if err in EMBED_DISABLED else "player error"
            raise SystemExit(f"embeddable: no ({verdict}, IFrame API error {err})")
        info = page.evaluate("seekTo(60)")
        if info["error"] is not None:
            raise SystemExit(f"embeddable: no (IFrame API error {info['error']})")
        duration = info["duration"]
        print(f"embeddable: yes  video {info['video_id']}  duration {duration:.0f} s  quality {info['quality']}")
        print(f"title: {info['title']}")
        if args.check:
            browser.close()
            return

        player = page.locator("#player")
        done = 0
        for n in range(n_frames):
            name = f"f_{n:04d}.png"
            old = rows.get(name)
            if old and (out_dir / name).exists() and not (args.redo_flagged and old["flags"]):
                continue
            t_target = start + interval * n
            flags = []
            for attempt in range(cap["retries"] + 1):
                tick = time.time()
                info = page.evaluate(f"seekTo({t_target})")
                if abs(info["duration"] - duration) > 1 or info["video_id"] != video["id"]:
                    write_index(index_path, rows)
                    raise SystemExit(f"{name}: the player is showing something else (advert?). Stopped; "
                                     "do not bypass it, switch to capture/ingest_manual.py.")
                player.screenshot(path=str(out_dir / name))
                drift = info["time"] - t_target
                flags = ["timing"] if abs(drift) > cap["max_drift_s"] else []
                time.sleep(max(0, cap["seek_gap_s"] - (time.time() - tick)))
                if not flags:
                    break
            if info["quality"] not in ("hd1080", "hd1440", "hd2160", "highres"):
                flags.append(f"quality_{info['quality']}")
            rows[name] = {"frame": name, "t_target": f"{t_target:.1f}", "t_actual": f"{info['time']:.2f}",
                          "flags": " ".join(flags)}
            write_index(index_path, rows)
            done += 1
            print(f"[{n + 1}/{n_frames}] {name} target {t_target:.1f} actual {info['time']:.2f} {' '.join(flags)}")
        browser.close()
    flagged = sum(1 for r in rows.values() if r["flags"])
    print(f"captured {done} new frames, {len(rows)} in {args.index}, {flagged} flagged, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
