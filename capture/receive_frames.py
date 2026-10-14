"""Receive frames grabbed in the maintainer's own browser and save them to disk.

  python capture/receive_frames.py                                   # -> data/frames, data/frames_index.csv
  python capture/receive_frames.py --out data/frames_coarse --index data/frames_coarse/index.csv

The NFL video cannot be embedded (IFrame error 150) and an automated browser cannot seek in it, so
frames are grabbed on the normal watch page in the maintainer's signed-in browser: the page-side
loop in capture/grab_in_page.js seeks, draws the <video> element to a canvas and POSTs the PNG here.
Only still frames are sent; the video stream is never downloaded.

  POST /frame?name=f_0000.png&t_target=5310.0&t_actual=5310.42&flags=   body = image bytes
"""
import argparse
import csv
import http.server
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["frame", "t_target", "t_actual", "flags"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/frames")
    ap.add_argument("--index", default="data/frames_index.csv")
    ap.add_argument("--port", type=int, default=8011)
    args = ap.parse_args()
    out_dir, index_path = ROOT / args.out, ROOT / args.index
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    if index_path.exists():
        with index_path.open(encoding="utf-8", newline="") as fh:
            rows = {r["frame"]: r for r in csv.DictReader(fh)}

    class Handler(http.server.BaseHTTPRequestHandler):
        def cors(self, code):
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "https://www.youtube.com")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_OPTIONS(self):
            self.cors(204)

        def do_GET(self):  # which frames are already here (for resuming)
            self.cors(200)
            self.wfile.write(",".join(sorted(n for n in rows if (out_dir / n).exists())).encode())

        def do_POST(self):
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query, keep_blank_values=True).items()}
            name = Path(q.get("name", "")).name
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if not name.startswith("f_") or not body:
                return self.cors(400)
            (out_dir / name).write_bytes(body)
            rows[name] = {"frame": name, "t_target": q.get("t_target", ""), "t_actual": q.get("t_actual", ""),
                          "flags": q.get("flags", "")}
            with index_path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows[k] for k in sorted(rows))
            print(f"{name}  target {q.get('t_target')}  actual {q.get('t_actual')}  {q.get('flags', '')}  {len(body):,} bytes", flush=True)
            self.cors(200)

        def log_message(self, *a):
            pass

    print(f"receiving frames on http://127.0.0.1:{args.port} -> {args.out}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
